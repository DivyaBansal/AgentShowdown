/** Says up front what the environment can and cannot do.
 *
 * Launching an agent needs sbx and a running sandbox daemon. Rather than
 * letting a run fail several layers down with a subprocess error, the app
 * asks first and says exactly which piece is missing.
 */

import type { Preflight } from "../api";

export function PreflightBanner({ preflight }: { preflight: Preflight | null }) {
  if (preflight === null) {
    return null;
  }

  const failing = preflight.checks.filter((check) => !check.ok);

  if (preflight.live_runs_possible && failing.length === 0) {
    return (
      <div className="card" role="status">
        <strong>Environment ready.</strong> Live runs can launch sandboxes.
      </div>
    );
  }

  return (
    <div className="card" role="alert">
      <strong>
        {preflight.live_runs_possible
          ? "Environment mostly ready."
          : "Live runs are unavailable."}
      </strong>
      <ul>
        {failing.map((check) => (
          <li key={check.name}>
            <code>{check.name}</code> — {check.detail}
          </li>
        ))}
      </ul>
      {!preflight.live_runs_possible && (
        <p className="hint">
          You can still browse recorded runs. Starting a new one needs the
          checks above to pass.
        </p>
      )}
    </div>
  );
}
