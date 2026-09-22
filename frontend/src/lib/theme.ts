/** Where the theme choice lives, and how it reaches the page.
 *
 * Stored in this browser's localStorage, not on the server: it is a
 * per-device display preference and the app has no user accounts to hang it
 * on. "system" is the default and stores nothing, so the OS setting applies.
 * An explicit choice stamps data-theme on <html>, which tokens.css already
 * responds to in both directions.
 *
 * index.html applies the stored value before first paint, so a dark choice
 * never flashes light while the bundle loads. It reads the same key; a test
 * keeps the two in step.
 */

export type ThemeChoice = "system" | "light" | "dark";

export const THEME_STORAGE_KEY = "agentshowdown.theme";

function isExplicit(value: unknown): value is "light" | "dark" {
  return value === "light" || value === "dark";
}

/** The saved choice, or "system" when nothing is saved or storage is blocked. */
export function readStoredTheme(): ThemeChoice {
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isExplicit(value) ? value : "system";
  } catch {
    // Storage can throw outright (blocked site data, some private modes).
    return "system";
  }
}

export function storeTheme(choice: ThemeChoice): void {
  try {
    if (choice === "system") {
      window.localStorage.removeItem(THEME_STORAGE_KEY);
    } else {
      window.localStorage.setItem(THEME_STORAGE_KEY, choice);
    }
  } catch {
    // Not persisted, but the choice still applies for this page view.
  }
}

export function applyTheme(choice: ThemeChoice, root: HTMLElement = document.documentElement): void {
  if (choice === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", choice);
  }
}
