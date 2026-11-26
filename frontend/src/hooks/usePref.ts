/** A useState that remembers its value across page loads. */

import { useCallback, useState } from "react";
import { readPref, storePref } from "../lib/prefs";

export function usePref<T extends string>(
  key: string,
  allowed: readonly T[],
  fallback: T,
): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => readPref(key, allowed, fallback));

  const update = useCallback(
    (next: T) => {
      setValue(next);
      storePref(key, next);
    },
    [key],
  );

  return [value, update];
}
