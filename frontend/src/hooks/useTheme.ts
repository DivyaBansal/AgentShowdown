/** The current theme choice, applied to the page and remembered. */

import { useEffect, useState } from "react";
import { applyTheme, readStoredTheme, storeTheme, type ThemeChoice } from "../lib/theme";

export function useTheme(): [ThemeChoice, (choice: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>(readStoredTheme);

  useEffect(() => {
    applyTheme(choice);
    storeTheme(choice);
  }, [choice]);

  return [choice, setChoice];
}
