/** Edit the active workspace's config.yaml.
 *
 * Grouped to mirror the YAML's own sections, so what you edit here maps
 * one-to-one onto what lands on disk.
 *
 * `test_command` with `verify_on: host` runs on the *host* via `bash -c`,
 * not in a sandbox, so changing it is gated behind a typed confirmation --
 * the same pattern SandboxControls uses for the host-wide wipe.
 *
 * Renders only the form body; the surrounding Panel supplies the heading.
 */

import { useEffect, useState } from "react";
import {
  ApiError,
  fetchConfig,
  saveConfig,
  type AgentInfo,
  type ConfigDoc,
  type ConfigValues,
} from "../api";
import { withoutDraftValues } from "../lib/config";
import { Field } from "./ui/Field";
import { Notice } from "./ui/Notice";

const CONFIRM_PHRASE = "RUN ON HOST";

export function ConfigEditor({
  reloadKey,
  agents = [],
  onDocChange,
}: {
  reloadKey: number;
  agents?: AgentInfo[];
  onDocChange?: (doc: ConfigDoc) => void;
}) {
  const [doc, setDoc] = useState<ConfigDoc | null>(null);
  const [values, setValues] = useState<ConfigValues | null>(null);
  // The repo's own remote, as detected by the server. Offered as a
  // placeholder rather than a value, since it is a guess about your repo.
  const [detectedRepo, setDetectedRepo] = useState("");
  const [originalTest, setOriginalTest] = useState("");
  const [confirm, setConfirm] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetchConfig()
      .then((d) => {
        const initial = d.exists ? d.config : withoutDraftValues(d.config);
        setDoc(d);
        setValues(initial);
        setDetectedRepo(d.exists ? "" : d.config.github.repo);
        setOriginalTest(initial.run.test_command);
        setConfirm("");
        onDocChange?.(d);
      })
      .catch(() => setDoc(null));
    // onDocChange is a notification, not an input to the fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey]);

  if (!doc || !values) {
    return <p className="hint">Select a repository to configure it.</p>;
  }

  const hostVerifyChanged =
    values.run.verify_on === "host" &&
    values.run.test_command !== "" &&
    values.run.test_command !== originalTest;
  const blocked = hostVerifyChanged && confirm !== CONFIRM_PHRASE;
  const agent = agents.find((a) => a.agent_id === values.agent.sbx_agent);

  function set<K extends keyof ConfigValues>(section: K, patch: Partial<ConfigValues[K]>) {
    setValues((v) => (v ? { ...v, [section]: { ...v[section], ...patch } } : v));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!values || !doc) return;
    setError(null);
    setStatus(null);
    setBusy(true);
    try {
      const saved = await saveConfig(values);
      setStatus(`Saved to ${saved.path}`);
      setOriginalTest(values.run.test_command);
      setConfirm("");
      const next = { ...doc, exists: true, config: values };
      setDoc(next);
      onDocChange?.(next);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save the configuration");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <p className="hint">
        {doc.exists ? (
          <>
            Loaded from <span className="mono">{doc.path}</span>
          </>
        ) : (
          <>
            No configuration saved at <span className="mono">{doc.path}</span> yet.
          </>
        )}
      </p>
      {!doc.editable && (
        <Notice tone="warn">
          This is the bundled demo preset and is read-only. Select a repository of
          your own to save changes.
        </Notice>
      )}

      <form className="controls" onSubmit={handleSubmit}>
        <fieldset className="group panel__section">
          <legend>GitHub</legend>
          <div className="field-grid">
            <Field id="github-repo" label="Repository (owner/name)">
              <input
                id="github-repo"
                value={values.github.repo}
                onChange={(e) => set("github", { repo: e.target.value })}
                placeholder={detectedRepo || "owner/repo"}
                required
              />
            </Field>
            <Field id="base-branch" label="Base branch">
              <input
                id="base-branch"
                value={values.github.base_branch}
                onChange={(e) => set("github", { base_branch: e.target.value })}
                placeholder="main"
              />
            </Field>
          </div>
          <label className="check">
            <input
              type="checkbox"
              checked={values.github.open_pr}
              onChange={(e) => set("github", { open_pr: e.target.checked })}
            />
            Open a pull request
          </label>
        </fieldset>

        <fieldset className="group panel__section">
          <legend>Agent defaults</legend>
          <div className="field-grid">
            <Field id="sbx-agent" label="Default agent">
              <input
                id="sbx-agent"
                list="config-agent-ids"
                value={values.agent.sbx_agent}
                onChange={(e) => set("agent", { sbx_agent: e.target.value })}
                placeholder="claude"
                required
              />
              <datalist id="config-agent-ids">
                {agents.map((a) => (
                  <option key={a.agent_id} value={a.agent_id} />
                ))}
              </datalist>
            </Field>
            <Field id="default-model" label="Default model">
              <input
                id="default-model"
                list="config-models"
                value={values.agent.model}
                onChange={(e) => set("agent", { model: e.target.value })}
                placeholder={agent?.default_model ?? "Model name"}
                required
              />
              <datalist id="config-models">
                {(agent?.known_models ?? []).map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
            </Field>
          </div>
          <label className="check">
            <input
              type="checkbox"
              checked={values.agent.dangerously_skip_permissions}
              onChange={(e) => set("agent", { dangerously_skip_permissions: e.target.checked })}
            />
            Skip permission prompts
          </label>
        </fieldset>

        <fieldset className="group panel__section">
          <legend>Run</legend>
          <div className="field-grid">
            <Field id="verify-on-config" label="Run tests on">
              <select
                id="verify-on-config"
                value={values.run.verify_on}
                onChange={(e) => set("run", { verify_on: e.target.value as "host" | "sandbox" })}
              >
                <option value="host">host (cheaper)</option>
                <option value="sandbox">sandbox</option>
              </select>
            </Field>
            <Field id="timeout-minutes" label="Timeout (minutes)">
              <input
                id="timeout-minutes"
                type="number"
                min={1}
                value={values.run.timeout_minutes}
                onChange={(e) => set("run", { timeout_minutes: Number(e.target.value) })}
              />
            </Field>
            <Field id="max-concurrency" label="Max concurrency">
              <input
                id="max-concurrency"
                type="number"
                min={1}
                value={values.run.max_concurrency}
                onChange={(e) => set("run", { max_concurrency: Number(e.target.value) })}
              />
            </Field>
          </div>
          <div className="field-grid">
            <Field id="test-command" label="Test command">
              <input
                id="test-command"
                className="mono"
                value={values.run.test_command}
                onChange={(e) => set("run", { test_command: e.target.value })}
                placeholder="pytest -q"
              />
            </Field>
            <Field id="lint-command" label="Lint command">
              <input
                id="lint-command"
                className="mono"
                value={values.run.lint_command}
                onChange={(e) => set("run", { lint_command: e.target.value })}
                placeholder="ruff check ."
              />
            </Field>
          </div>
        </fieldset>

        {hostVerifyChanged && (
          <div className="stack stack--tight">
            <Notice tone="warn">
              This test command runs on <strong>the host</strong>, not in a sandbox.
              Type <code>{CONFIRM_PHRASE}</code> to confirm.
            </Notice>
            <Field id="host-confirm" label="Confirmation">
              <input
                id="host-confirm"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
              />
            </Field>
          </div>
        )}

        <div className="actions">
          <button
            type="submit"
            className="btn--primary"
            disabled={busy || blocked || !doc.editable}
          >
            {busy ? "Saving…" : "Save configuration"}
          </button>
        </div>
      </form>

      {status && <Notice tone="ok">{status}</Notice>}
      {error && <Notice tone="danger">{error}</Notice>}
    </>
  );
}
