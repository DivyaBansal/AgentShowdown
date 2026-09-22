/** Choose the repository agentshowdown works against.
 *
 * Two ways in: a path already on this machine, or a GitHub URL to clone.
 * Inspecting is deliberately separate from selecting -- looking at a folder
 * shouldn't change which one is in use -- so the result panel reports what
 * was found and selecting is an explicit second click.
 *
 * Renders only the body; the surrounding Panel supplies the heading.
 */

import { useEffect, useState } from "react";
import {
  ApiError,
  cloneRepo,
  fetchCloneStatus,
  fetchWorkspaces,
  inspectWorkspace,
  selectWorkspace,
  type WorkspaceInfo,
  type WorkspaceList,
} from "../api";
import { Field } from "./ui/Field";
import { Notice } from "./ui/Notice";
import { StatusChip } from "./ui/StatusChip";

/** How often to ask whether a clone has finished. Polling the status
 *  endpoint rather than subscribing to SSE keeps this to one EventSource for
 *  the whole app, and survives a missed event -- the clone is short-lived and
 *  the endpoint exists precisely as that fallback. */
const CLONE_POLL_MS = 1000;

type Mode = "local" | "github";

export function WorkspacePicker({ onChanged }: { onChanged: () => void }) {
  const [mode, setMode] = useState<Mode>("local");
  const [path, setPath] = useState("");
  const [url, setUrl] = useState("");
  const [found, setFound] = useState<WorkspaceInfo | null>(null);
  const [known, setKnown] = useState<WorkspaceList>({ active: null, workspaces: [] });
  const [cloneId, setCloneId] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function reload() {
    fetchWorkspaces()
      .then(setKnown)
      .catch(() => setKnown({ active: null, workspaces: [] }));
  }

  useEffect(reload, []);

  // A clone runs on a background thread, so the request returns immediately
  // and the outcome has to be collected separately. Without this the progress
  // bar spins forever even though the clone finished.
  useEffect(() => {
    if (cloneId === null) return;
    let cancelled = false;

    const timer = setInterval(() => {
      fetchCloneStatus(cloneId)
        .then((job) => {
          if (cancelled || job.state === "running") return;
          setCloneId(null);
          if (job.state === "finished") {
            setStatus(`Cloned into ${job.target_path}. Now in use.`);
            reload();
            onChanged();
          } else {
            setError(job.detail || "The clone failed");
          }
        })
        .catch(() => {
          if (cancelled) return;
          setCloneId(null);
          setError("Lost track of the clone");
        });
    }, CLONE_POLL_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cloneId]);

  function fail(err: unknown, fallback: string) {
    setError(err instanceof ApiError ? err.message : fallback);
  }

  async function handleInspect(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setStatus(null);
    setFound(null);
    setBusy(true);
    try {
      if (mode === "local") {
        setFound(await inspectWorkspace(path));
      } else {
        const job = await cloneRepo(url);
        setCloneId(job.clone_id);
        setStatus(`Cloning into ${job.target_path}…`);
      }
    } catch (err) {
      fail(err, mode === "local" ? "Could not inspect that path" : "Could not start the clone");
    } finally {
      setBusy(false);
    }
  }

  async function handleUse(target: string) {
    setError(null);
    try {
      await selectWorkspace(target);
      setStatus(`Using ${target}`);
      setFound(null);
      reload();
      onChanged();
    } catch (err) {
      fail(err, "Could not select that repository");
    }
  }

  return (
    <>
      <form className="controls" onSubmit={handleInspect}>
        <fieldset className="group">
          <legend>Source</legend>
          <div className="checks">
            <label className="check">
              <input
                type="radio"
                name="repo-source"
                checked={mode === "local"}
                onChange={() => setMode("local")}
              />
              A folder on this machine
            </label>
            <label className="check">
              <input
                type="radio"
                name="repo-source"
                checked={mode === "github"}
                onChange={() => setMode("github")}
              />
              A GitHub URL to clone
            </label>
          </div>
        </fieldset>

        {mode === "local" ? (
          <Field id="repo-path" label="Repository path">
            <div className="repeat-row">
              <input
                id="repo-path"
                className="mono"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder="/home/you/code/your-repo"
                required
              />
              <button type="submit" disabled={busy}>
                Inspect
              </button>
            </div>
          </Field>
        ) : (
          <Field
            id="repo-url"
            label="GitHub URL"
            hint="Cloned with full history. A shallow clone would make every diff measure zero."
          >
            <div className="repeat-row">
              <input
                id="repo-url"
                className="mono"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://github.com/owner/repo"
                required
              />
              <button type="submit" disabled={busy}>
                Clone
              </button>
            </div>
          </Field>
        )}
      </form>

      {cloneId !== null && <progress aria-label="Cloning repository" />}

      {found && (
        <div className="surface">
          <div className="surface__head">
            <h3 className="mono">{found.path}</h3>
            <StatusChip tone={found.is_git_repo ? "ok" : "danger"}>
              {found.is_git_repo ? "git repository" : "not a git repository"}
            </StatusChip>
          </div>
          <div className="surface__body">
            <ul className="list">
              {found.github_repo && <li>GitHub: {found.github_repo}</li>}
              {found.default_branch && <li>Default branch: {found.default_branch}</li>}
              <li>{found.has_config ? "Existing config found." : "No config yet."}</li>
              <li>{found.has_features ? "Existing features found." : "No features yet."}</li>
            </ul>
            <div className="actions">
              <button
                type="button"
                className="btn--primary"
                onClick={() => void handleUse(found.path)}
              >
                Use this repository
              </button>
            </div>
          </div>
        </div>
      )}

      {known.workspaces.length > 0 && (
        <div className="panel__section stack stack--tight">
          <h3 className="eyebrow">Known repositories</h3>
          <ul className="list">
            {known.workspaces.map((w) => (
              <li key={w.path}>
                <span className="list__main mono">{w.path}</span>
                {w.path === known.active ? (
                  <StatusChip tone="ok">in use</StatusChip>
                ) : (
                  <button type="button" onClick={() => void handleUse(w.path)}>
                    Switch
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {status && <Notice tone="ok">{status}</Notice>}
      {error && <Notice tone="danger">{error}</Notice>}
    </>
  );
}
