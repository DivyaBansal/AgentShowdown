/** Draft features from GitHub issues.
 *
 * Only rendered when the active workspace has a GitHub remote. Each imported
 * issue becomes a feature with a deterministic `issue-<n>` id, so importing
 * the same issue twice updates it rather than making a near-duplicate.
 *
 * Renders only the body; the caller supplies the heading.
 */

import { useState } from "react";
import { ApiError, fetchIssues, importIssues, type GithubIssue } from "../api";
import { Notice } from "./ui/Notice";

export function IssueImporter({ repo, onImported }: { repo: string; onImported: () => void }) {
  const [issues, setIssues] = useState<GithubIssue[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function fail(err: unknown, fallback: string) {
    setError(err instanceof ApiError ? err.message : fallback);
  }

  async function handleLoad() {
    setError(null);
    setBusy(true);
    try {
      const result = await fetchIssues(repo);
      setIssues(result.issues);
      setLoaded(true);
    } catch (err) {
      fail(err, "Could not load issues");
    } finally {
      setBusy(false);
    }
  }

  async function handleImport() {
    setError(null);
    setStatus(null);
    setBusy(true);
    try {
      const result = await importIssues(selected);
      setStatus(`Imported ${result.imported} issue(s)`);
      setSelected([]);
      onImported();
    } catch (err) {
      fail(err, "Could not import the selected issues");
    } finally {
      setBusy(false);
    }
  }

  function toggle(number: number) {
    setSelected((current) =>
      current.includes(number)
        ? current.filter((n) => n !== number)
        : [...current, number],
    );
  }

  return (
    <div className="stack">
      <p className="hint">
        Reads <span className="mono">{repo}</span> through the <code>gh</code> CLI, so
        your existing GitHub login is used and no token is stored here.
      </p>

      <div className="actions">
        <button type="button" onClick={() => void handleLoad()} disabled={busy}>
          {loaded ? "Reload issues" : "Load issues"}
        </button>
      </div>

      {loaded && issues.length === 0 && <p className="hint">No open issues found.</p>}

      {issues.length > 0 && (
        <>
          <fieldset className="group">
            <legend>Open issues</legend>
            <ul className="list">
              {issues.map((issue) => (
                <li key={issue.number}>
                  <label className="check list__main">
                    <input
                      type="checkbox"
                      checked={selected.includes(issue.number)}
                      onChange={() => toggle(issue.number)}
                    />
                    <span>
                      <span className="num hint">#{issue.number}</span> {issue.title}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>
          <div className="actions">
            <button
              type="button"
              className="btn--primary"
              onClick={() => void handleImport()}
              disabled={busy || selected.length === 0}
            >
              Import selected
            </button>
          </div>
        </>
      )}

      {status && <Notice tone="ok">{status}</Notice>}
      {error && <Notice tone="danger">{error}</Notice>}
    </div>
  );
}
