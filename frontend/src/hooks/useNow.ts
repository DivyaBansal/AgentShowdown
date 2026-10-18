/** The current time, re-rendering every `intervalMs` while `active`.
 *
 * Inactive costs nothing: no timer runs, so a page of finished jobs never
 * re-renders on its own.
 */

import { useEffect, useState } from "react";

export function useNow(active: boolean, intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), intervalMs);

    return () => clearInterval(timer);
  }, [active, intervalMs]);

  return now;
}
