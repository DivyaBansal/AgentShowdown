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
    const tick = () => setNow(Date.now());
    // Tick once straight away so a clock that was paused while inactive
    // doesn't show a stale time for a whole interval.
    const first = setTimeout(tick, 0);
    const timer = setInterval(tick, intervalMs);

    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [active, intervalMs]);

  return now;
}
