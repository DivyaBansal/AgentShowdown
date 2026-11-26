/** Small per-device view preferences, kept in localStorage.
 *
 * Like the theme (lib/theme.ts), these are display choices with no account
 * to hang them on. Every read is validated against the allowed values, so a
 * stale or hand-edited entry falls back to the default instead of putting
 * the UI in a state no control can reach.
 */

const PREFIX = "agentshowdown.";

/** The stored value for `key` if it is one of `allowed`, else `fallback`. */
export function readPref<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = window.localStorage.getItem(PREFIX + key);

    return allowed.find((option) => option === value) ?? fallback;
  } catch {
    // Storage can throw outright (blocked site data, some private modes).
    return fallback;
  }
}

export function storePref(key: string, value: string): void {
  try {
    window.localStorage.setItem(PREFIX + key, value);
  } catch {
    // Not persisted, but the choice still applies for this page view.
  }
}
